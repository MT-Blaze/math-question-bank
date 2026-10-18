import os
import json
import pytest
from unittest.mock import MagicMock, patch

from main import LOCAL_TOKEN
from mathbank.database import Paper, PaperQuestion, Question

def test_paper_api_flow(client, stubbed_full_backup):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    
    # 1. Create a question
    q_data = {
        "content": "已知函数 $f(x) = x^2 + 2x + 1$，求 $f(1)$ 的值。\n\\begin{choices}\n\\item 1\n\\item 2\n\\item 4\n\\item 8\n\\end{choices}",
        "question_type": "single_choice",
        "category_compulsory": "必修第一册",
        "category_chapter": "第一章 集合与常用逻辑用语",
        "category_knowledge": "集合的概念",
        "difficulty": "easy",
        "source": "2026月考",
        "answer_markdown": "解：$f(1) = 1^2 + 2(1) + 1 = 4$。故选 C。",
        "review": "易错点：计算细节",
        "tags": "函数,计算",
        "related_question_id": "",
        "image_paths": "[]"
    }
    create_res = client.post("/api/questions", data=q_data, headers=headers)
    assert create_res.status_code == 200
    q_id = create_res.json()["question"]["id"]
    
    # 2. Batch fetch paper questions
    fetch_res = client.get(f"/api/paper/questions?ids={q_id}")
    assert fetch_res.status_code == 200
    assert len(fetch_res.json()["data"]) == 1
    assert fetch_res.json()["data"][0]["id"] == q_id
    
    # 3. Save Paper & verify usage_count increment
    paper_payload = {
        "title": "测试高中数学单元测试卷",
        "subtitle": "满分150分",
        "paper_type": "exam",
        "questions": [{"id": q_id, "score": 5, "order": 1}]
    }
    save_res = client.post("/api/paper/save", json=paper_payload, headers=headers)
    print("SAVE_RES:", save_res.json())
    assert save_res.status_code == 200
    assert save_res.json()["status"] == "success"
    
    # Check question usage count
    get_q_res = client.get(f"/api/questions/{q_id}")
    assert get_q_res.status_code == 200
    assert get_q_res.json()["usage_count"] == 1

    # 4. Export LaTeX TEX Zip
    tex_res = client.post("/api/paper/export/tex", json=paper_payload, headers=headers)
    assert tex_res.status_code == 200
    assert tex_res.headers.get("content-type") == "application/zip"

    # 4b. Export Full Bundle Zip (TeX + PDF)
    bundle_res = client.post("/api/paper/export/bundle", json=paper_payload, headers=headers)
    assert bundle_res.status_code == 200
    assert bundle_res.headers.get("content-type") == "application/zip"

    # 5. AI Paper Selection
    ai_res = client.post("/api/paper/ai-select", json={"prompt": "函数", "limit": 5}, headers=headers)
    assert ai_res.status_code == 200
    assert ai_res.json()["status"] == "success"
    assert len(ai_res.json()["data"]) >= 1

    # 6. Saved Papers List & Detail & Delete
    list_res = client.get("/api/papers")
    assert list_res.status_code == 200
    papers_list = list_res.json()["data"]
    assert len(papers_list) >= 1
    paper_id = papers_list[0]["id"]

    detail_res = client.get(f"/api/papers/{paper_id}")
    assert detail_res.status_code == 200
    assert detail_res.json()["data"]["title"] == "测试高中数学单元测试卷"
    assert len(detail_res.json()["data"]["questions"]) == 1

    del_res = client.delete(f"/api/papers/{paper_id}", headers=headers)
    assert del_res.status_code == 200
    assert len(stubbed_full_backup) == 1, "删卷前没有登记备份"
    assert del_res.json()["status"] == "success"


def test_ai_paper_selection_uses_shared_provider_and_clean_effort(
    client, db_session
):
    question = Question(
        content="求函数的导数",
        question_type="detailed_answer",
        difficulty="medium",
    )
    excluded_question = Question(
        content="不应越过类型筛选的选择题",
        question_type="single_choice",
        difficulty="medium",
    )
    db_session.add_all([question, excluded_question])
    db_session.commit()
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "selected_ids": [
                                excluded_question.id,
                                question.id,
                                question.id,
                            ],
                            "ai_analysis": "选取一道导数题",
                        },
                        ensure_ascii=False,
                    )
                }
            }
        ]
    }
    provider_env = {
        "PREFER_SOLVE_MODEL": "ZHONGZHAN_GPT/gpt-5.6-sol:high",
        "ZHONGZHAN_GPT_API_KEY": "transit-key",
        "ZHONGZHAN_GPT_BASE_URL": (
            "https://transit.example/v1/chat/completions/"
        ),
    }

    with patch.dict(os.environ, provider_env, clear=False), patch(
        "mathbank.ai_http.robust_request_post", return_value=response
    ) as mock_post:
        result = client.post(
            "/api/paper/ai-select",
            json={
                "prompt": "请选 1 道函数题",
                "question_type": "detailed_answer",
                "limit": 1,
            },
            headers={"X-Local-Token": LOCAL_TOKEN},
        )

    assert result.status_code == 200
    assert result.json()["fallback"] is False
    assert [item["id"] for item in result.json()["data"]] == [question.id]
    args, kwargs = mock_post.call_args
    assert args[0] == "https://transit.example/v1/chat/completions"
    assert kwargs["json"]["model"] == "gpt-5.6-sol"
    assert kwargs["json"]["reasoning_effort"] == "high"
    assert kwargs["json"]["enable_thinking"] is True


@pytest.mark.parametrize(
    "questions",
    [
        [{"id": 999999, "score": 5}],
        [{"id": 1, "score": 5}, {"id": 1, "score": 5}],
        [{"id": 1, "score": -1}],
    ],
)
def test_invalid_paper_items_are_rejected_without_partial_rows(
    client, db_session, questions
):
    question = Question(content="组卷事务题", question_type="single_choice")
    db_session.add(question)
    db_session.commit()
    # Parameter values use the first SQLite ID in this isolated fixture.
    for item in questions:
        if item["id"] == 1:
            item["id"] = question.id

    result = client.post(
        "/api/paper/save",
        json={"title": "无效试卷", "paper_type": "exam", "questions": questions},
        headers={"X-Local-Token": LOCAL_TOKEN},
    )

    assert result.status_code == 400
    assert db_session.query(Paper).count() == 0
    assert db_session.query(PaperQuestion).count() == 0
    db_session.refresh(question)
    assert question.usage_count in {None, 0}

def test_exam_19_and_answer_sheet_generation(client):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    
    from mathbank.paper_helper import build_answer_sheet_latex
    
    questions_data = [
        {
            "question": {
                "id": 101,
                "question_type": "detailed_answer",
                "content": "求证：$\\sin^2 x + \\cos^2 x = 1$。\n\\begin{tikzpicture}\n\\draw (0,0) -- (1,1);\n\\end{tikzpicture}"
            },
            "score": 13
        }
    ]
    
    sheet_tex = build_answer_sheet_latex("2026年模拟考试试卷", "", questions_data)
    assert "\\documentclass" in sheet_tex
    assert "2026年模拟考试试卷" in sheet_tex
    assert "15.（13分）" in sheet_tex
    assert "\\begin{tikzpicture}" in sheet_tex
    
    # Test export endpoints for exam_19
    paper_payload = {
        "title": "2026年高考模拟试卷",
        "subtitle": "数学",
        "paper_type": "exam_19",
        "questions": []
    }
    
    export_res = client.post("/api/paper/export/tex", json=paper_payload, headers=headers)
    assert export_res.status_code == 200
    assert export_res.headers.get("content-type") == "application/zip"

    pdf_sheet_payload = {
        "title": "2026年高考模拟试卷",
        "subtitle": "数学",
        "paper_type": "exam_19",
        "target": "sheet",
        "questions": []
    }
    # The endpoint contract should not depend on XeLaTeX being installed on
    # the CI runner; compilation behavior has separate focused tests.
    with patch(
        "main.compile_tex_to_pdf",
        return_value=(b"%PDF-1.7\n% deterministic test payload", ""),
    ):
        sheet_pdf_res = client.post(
            "/api/paper/export/pdf", json=pdf_sheet_payload, headers=headers
        )
    assert sheet_pdf_res.status_code == 200
    assert sheet_pdf_res.headers.get("content-type") == "application/pdf"

def test_solution_space_latex_generation():
    from mathbank.paper_helper import build_latex_document
    questions_data_65 = [{
        "question": {
            "id": 99,
            "question_type": "detailed_answer",
            "content": "已知函数 $f(x) = ax^2 + bx + c$，求导数 $f'(x)$。",
            "figure_align": "right"
        },
        "score": 12,
        "solution_space": "6.5"
    }]
    questions_data_zero = [{
        "question": {
            "id": 99,
            "question_type": "detailed_answer",
            "content": "已知函数 $f(x) = ax^2 + bx + c$，求导数 $f'(x)$。",
            "figure_align": "right"
        },
        "score": 12,
        "solution_space": "0.0"
    }]
    
    # 1. Non-exam_19 paper mode -> should inject \vspace*{6.5cm}
    tex = build_latex_document("单元测试", "试卷", "exam", questions_data_65, include_answers=False)
    assert r"\vspace*{6.5cm}" in tex
    
    # 2. exam_19 paper mode with 3.0cm solution space -> should inject \vspace*{3.0cm}
    questions_data_3 = [{
        "question": {
            "id": 99,
            "question_type": "detailed_answer",
            "content": "已知函数 $f(x) = ax^2 + bx + c$，求导数 $f'(x)$。",
            "figure_align": "right"
        },
        "score": 12,
        "solution_space": "3.0"
    }]
    tex_19_three = build_latex_document("高考模拟", "试卷", "exam_19", questions_data_3, include_answers=False)
    assert r"\vspace*{3.0cm}" in tex_19_three

    # 3. exam_19 paper mode with 0.0cm solution space -> should not inject \vspace
    tex_19_zero = build_latex_document("高考模拟", "试卷", "exam_19", questions_data_zero, include_answers=False)
    assert r"\vspace*" not in tex_19_zero


def test_latex_export_renders_every_editable_content_tikz_asset_once():
    from mathbank.paper_helper import build_latex_document

    first_code = r"\begin{tikzpicture}\draw (0,0)--(1,0);\end{tikzpicture}"
    second_code = r"\begin{tikzpicture}\draw (0,0) circle (1);\end{tikzpicture}"
    questions = [{
        "question": {
            "id": 100,
            "question_type": "detailed_answer",
            "content": (
                "多图测试\n\n![第一图](/static/uploads/tikz_first.png)"
                "\n\n![第二图](/static/uploads/tikz_second.png)"
                "\n\n![普通图](/static/uploads/photo.png)"
            ),
            "content_tikz_assets": [
                {
                    "id": "content_first",
                    "image_path": "/static/uploads/tikz_first.png",
                    "tikz_code": first_code,
                },
                {
                    "id": "content_second",
                    "image_path": "/static/uploads/tikz_second.png",
                    "tikz_code": second_code,
                },
            ],
            "tikz_code": first_code,
            "figure_align": "center",
        },
        "score": 12,
    }]

    tex = build_latex_document("多图测试", "", "exam", questions)

    assert tex.count(first_code) == 1
    assert tex.count(second_code) == 1
    assert "tikz_first.png" not in tex
    assert "tikz_second.png" not in tex
    assert r"\includegraphics[width=3.8cm]{photo.png}" in tex


def test_latex_export_preserves_complex_inline_and_tabular_image_positions():
    from mathbank.paper_helper import build_latex_document

    content = (
        "【课本回顾】如图 1，在三角形中研究中线。\n\n"
        "![图1和图2](/static/uploads/fig12.png)\n\n"
        "【知识探究】比较下面两种思路。\n\n"
        r"\begin{tabular}{|c|c|c|}" "\n"
        r" & 思路一 & 思路二 \\" "\n"
        r"\multirow{2}{*}{第一步} & 证明一 & 证明二 \\" "\n"
        r" & 推导一 & 推导二 \\" "\n"
        r"图形表达 & ![图3](/static/uploads/fig3.png) & "
        r"![图4](/static/uploads/fig4.png) \\" "\n"
        r"\end{tabular}"
    )
    questions = [{
        "question": {
            "id": 102,
            "question_type": "detailed_answer",
            "content": content,
            "figure_align": "right",
        },
        "score": 12,
        "solution_space": "0.0",
    }]

    tex = build_latex_document("复杂图文题", "", "exam", questions)

    first = tex.index("fig12.png")
    knowledge = tex.index("【知识探究】")
    table_start = tex.index(r"\begin{tabularx}")
    third = tex.index("fig3.png")
    fourth = tex.index("fig4.png")
    table_end = tex.index(r"\end{tabularx}")
    assert first < knowledge < table_start < third < fourth < table_end
    table_tex = tex[table_start:table_end]
    assert r"\multirow{2}{*}{第一步}" in table_tex
    assert table_tex.count(r"\adjustbox{valign=m,margin=0pt 3pt}{") == 2
    assert r"\includegraphics[max width=\linewidth,max height=4.0cm,keepaspectratio]{fig3.png}" in table_tex
    assert r"\includegraphics[max width=\linewidth,max height=4.0cm,keepaspectratio]{fig4.png}" in table_tex
    assert r"\noindent\begin{tabularx}{\linewidth}" in tex
    assert r">{\centering\arraybackslash}m{4em}" in tex
    assert tex.count(r">{\centering\arraybackslash}X") == 2
    assert r"\end{tabularx}" in tex


def test_latex_export_keeps_single_trailing_figure_alignment_layout():
    from mathbank.paper_helper import build_latex_document

    questions = [{
        "question": {
            "id": 103,
            "question_type": "detailed_answer",
            "content": "普通右图题\n\n![](/static/uploads/graph.png)",
            "figure_align": "right",
        },
        "score": 12,
        "solution_space": "0.0",
    }]

    tex = build_latex_document("普通右图题", "", "exam", questions)

    assert r"\begin{minipage}[t]{\dimexpr\linewidth-5.8cm\relax}" in tex
    assert r"\includegraphics[width=5.0cm]{graph.png}" in tex


def test_latex_export_bounds_full_width_multicolumn_and_keeps_multirow():
    from mathbank.paper_helper import build_latex_document

    long_title = "综合探究表格总标题需要在页面正文宽度内自动换行显示"
    content = (
        r"\begin{tabular}{|c|c|c|}" "\n"
        rf"\hline \multicolumn{{3}}{{|c|}}{{{long_title}}} \\" "\n"
        r"\hline \multirow{2}{*}{步骤} & 思路一 & 思路二 \\" "\n"
        r"\cline{2-3} & 完成证明一 & 完成证明二 \\" "\n"
        r"\hline\end{tabular}"
    )
    questions = [{
        "question": {
            "id": 104,
            "question_type": "detailed_answer",
            "content": content,
        },
        "score": 12,
        "solution_space": "0.0",
    }]

    tex = build_latex_document("合并表格题", "", "exam", questions)

    assert r"\noindent\begin{tabularx}{\linewidth}" in tex
    assert r"\multicolumn{3}{|>{\centering\arraybackslash}p{\dimexpr\linewidth-2\tabcolsep-2\arrayrulewidth\relax}|}" in tex
    assert long_title in tex
    assert r"\multirow{2}{*}{步骤}" in tex
    assert r"\cline{2-3}" in tex


@pytest.mark.parametrize(
    "environment",
    ["tabular", "tabular*", "tabularx", "longtable", "tblr", "longtblr", "talltblr"],
)
def test_latex_table_environments_keep_images_in_lr_safe_cells(environment):
    from mathbank.paper_helper import clean_content_for_latex

    source = (
        rf"\begin{{{environment}}}{{cc}}"
        r"图形表达 & ![](/static/uploads/cell.png) \\"
        rf"\end{{{environment}}}"
    )

    cleaned = clean_content_for_latex(
        source,
        preserve_image_positions=True,
    )

    image_position = cleaned.index("cell.png")
    begin_position = cleaned.index(rf"\begin{{{environment}}}")
    end_position = cleaned.index(rf"\end{{{environment}}}")
    assert begin_position < image_position < end_position
    assert r"\adjustbox{valign=m,margin=0pt 3pt}{" in cleaned
    assert r"\begin{center}" not in cleaned[begin_position:end_position]


def test_right_figure_choice_stem_has_no_extra_first_line_indent():
    from mathbank.paper_helper import build_latex_document

    questions = [{
        "question": {
            "id": 101,
            "question_type": "single_choice",
            "content": (
                "已知函数 $f(x)=x$，则 $f(1)=$\n"
                "\\begin{choices}\n"
                "\\item $0$\n"
                "\\item $1$\n"
                "\\item $2$\n"
                "\\item $3$\n"
                "\\end{choices}\n\n"
                "![](/static/uploads/graph.png)"
            ),
            "figure_align": "right",
        },
        "score": 5,
    }]

    tex = build_latex_document("右图选择题", "", "exam", questions)

    left_column = tex.index(r"\begin{minipage}[t]{\dimexpr\linewidth-5.8cm\relax}")
    first_stem = tex.index(r"\noindent 已知函数", left_column)
    right_column_end = tex.index(r"\end{minipage}", tex.index(r"\hfill", first_stem))
    choices = tex.index(r"\begin{choices}", first_stem)
    assert left_column < first_stem < right_column_end < choices


def test_choice_options_layout_compact_in_latex_export():
    r"""三种真实选项形态在导出端都必须转成 choices 环境横向排版。

    线上实测（MinerU 云端解析）选项有三种形态：
      ① 「A」标号一行、图形另起一行（第 1 题，四张曲线图）；
      ② 单行多选项「A．106 B．105 C．104 D．103」（第 5 题）；
      ③ 单行多选项且标号前无空格「…递增C．…」（第 3 题）。
    不转换的话每个选项在 A4 上独占一大段（图形还被 \begin{center} 放大成
    0.85 倍行宽居中），一道选择题竖着占半页。
    """

    from mathbank.paper_helper import clean_content_for_latex

    # 形态①：标号与图形分行
    stacked = (
        "下列各曲线中，不能表示 $y$ 是 $x$ 的函数的是（ ）\n\n"
        "A\n\n![](/static/uploads/mineru_9.jpg)\n\n"
        "B\n\n![](/static/uploads/mineru_5.jpg)\n\n"
        "C\n\n![](/static/uploads/mineru_2.jpg)\n\n"
        "D\n\n![](/static/uploads/mineru_7.jpg)"
    )
    out = clean_content_for_latex(stacked, q_type="single_choice")
    assert out.count(r"\begin{choices}") == 1
    assert out.count(r"\item") == 4
    # 选项图必须内联进 \item（限宽），绝不允许 \begin{center} 独占整行
    assert out.count(r"\begin{center}") == 0
    assert out.count("includegraphics") == 4
    assert "0.42" in out

    # 形态②：单行多选项（数学要原样保留，不许被当标号切开）
    inline = (
        "回归方程为 $\\hat{y}=36x-48$，则第4周治愈人数为（ ）\n\n"
        "A．106 B．105 C．104 D．103"
    )
    out2 = clean_content_for_latex(inline, q_type="single_choice")
    assert out2.count(r"\item") == 4
    assert r"\item 106" in out2 and r"\item 103" in out2
    assert "36x-48" in out2.replace(" ", "")

    # 形态③：标号前无空格
    tight = (
        "下列说法不正确的是（ ）\n\n"
        "A．该地水质差湖泊总量逐年递减 B．该地水质好湖泊总量逐年递增"
        "C．该地平均每年新增 10个湖泊 D．该地平均每年新增至少45个水质好湖泊"
    )
    out3 = clean_content_for_latex(tight, q_type="single_choice")
    assert out3.count(r"\item") == 4


def test_strip_image_reference_keeps_only_bare_label_option_images():
    r"""「整行选项」后面的配图是普通题干图，必须被剥离（第 5 题吞图线上实测）。

    `A．106 B．105 C．104 D．103` 虽以 `A．` 开头，但它是完整的选项行；
    只有**裸标号行**（整行只有 A / A．）之后的纯图片行才是选项图。
    """

    from mathbank.parsing_pipeline import _strip_image_reference

    leaked = (
        "回归方程为（ ）\n\n"
        "A．106 B．105 C．104 D．103\n\n"
        "![](/static/uploads/mineru_1789722233_3.jpg)"
    )
    fixed = _strip_image_reference(leaked, "/static/uploads/mineru_1789722233_3.jpg")
    assert "mineru_1789722233_3.jpg" not in fixed

    # 裸标号下一行的选项图仍然保留
    keep = "题干（ ）\n\nA\n\n![](/static/uploads/mineru_9.jpg)"
    kept = _strip_image_reference(keep, "/static/uploads/mineru_9.jpg")
    assert "mineru_9.jpg" in kept


def test_strip_image_reference_keeps_images_on_choices_items():
    r"""choices 环境内 `\item` 行上的图必须保留（线上第 1 题「选项图全丢」的根因）。

    整页 OCR 的提示词（COMMON_OCR_PROMPT 第 3 条）要求**剥离 A/B/C/D 标号**，
    于是它产出的是 `\item [ILLUSTRATION_BOX:...]`；裁图替换后变成
    `\item ![插图](tmp/pdf_crop_*.png)` —— 行首是 `\item` 而不是标号，
    只认标号的判据完全认不出来，四个选项图被逐个剥掉，题干只剩四个空壳。
    choices 是**机器生成的确定性结构标记**，环境内 `\item` 行的图片按定义
    就是该选项的内容，必须保留。
    """

    from mathbank.parsing_pipeline import _strip_image_reference

    content = (
        "1.下列各曲线中，不能表示 $y$ 是 $x$ 的函数的是（ ）\n\n"
        "\\begin{choices}\n"
        "\\item ![插图](tmp/pdf_crop_t_0_0.png)\n"
        "\\item ![插图](tmp/pdf_crop_t_0_1.png)\n"
        "\\item ![插图](tmp/pdf_crop_t_0_2.png)\n"
        "\\item ![插图](tmp/pdf_crop_t_0_3.png)\n"
        "\\end{choices}"
    )
    for i in range(4):
        url = f"/static/uploads/tmp/pdf_crop_t_0_{i}.png"
        content = _strip_image_reference(content, url)

    # 四张选项图一张都不能少：这是选项本身，删了题就废了
    for i in range(4):
        assert f"pdf_crop_t_0_{i}.png" in content, f"第 {i} 个选项图被误删"
    assert content.count("\\item") == 4


def test_strip_image_reference_still_strips_images_outside_choices_env():
    r"""choices 环境**之外**的图照旧剥离，别让新判据把普通配图留在正文里。"""

    from mathbank.parsing_pipeline import _strip_image_reference

    content = (
        "题干见图：![](/static/uploads/mineru_a.jpg)\n\n"
        "\\begin{choices}\n"
        "\\item ![插图](/static/uploads/mineru_b.jpg)\n"
        "\\item 甲\n\\item 乙\n\\item 丙\n"
        "\\end{choices}\n\n"
        "\\item ![插图](/static/uploads/mineru_c.jpg)"
    )
    fixed = _strip_image_reference(content, "/static/uploads/mineru_a.jpg")
    fixed = _strip_image_reference(fixed, "/static/uploads/mineru_c.jpg")
    assert "mineru_a.jpg" not in fixed   # 环境前的题干图 -> 剥离
    assert "mineru_c.jpg" not in fixed   # 环境后的游离 \item 行 -> 剥离
    assert "mineru_b.jpg" in fixed       # 环境内的选项图 -> 保留


def test_strip_image_reference_keeps_image_below_bare_item_line():
    r"""「裸 `\item` 一行、图形另起一行」的选项图必须保留（线上第 1 题空选项的根因）。

    OCR 提示词要求剥离 A-D 标号，AI 实测产出的主要形态是「`\item` 单独一行、
    下一行只有图片」。这与「裸标号 A 一行、图片另起一行」同构，但旧判据
    只认标号行（`_BARE_CHOICE_LABEL_RE`），`\item` 行不在其列，于是四个选项图
    被逐个剥掉，题干退化成四个空壳 `\item`（第 1 题实测就是如此）。
    """

    from mathbank.parsing_pipeline import _strip_image_reference

    content = (
        "1.下列各曲线中，不能表示 $y$ 是 $x$ 的函数的是（ ）\n\n"
        "\\begin{choices}\n"
        "\\item\n"
        "![插图](tmp/pdf_crop_t_0_0.png)\n"
        "\\item\n"
        "![插图](tmp/pdf_crop_t_0_1.png)\n"
        "\\item\n"
        "![插图](tmp/pdf_crop_t_0_2.png)\n"
        "\\item\n"
        "![插图](tmp/pdf_crop_t_0_3.png)\n"
        "\\end{choices}"
    )
    for i in range(4):
        content = _strip_image_reference(content, f"/static/uploads/tmp/pdf_crop_t_0_{i}.png")

    for i in range(4):
        assert f"pdf_crop_t_0_{i}.png" in content, f"第 {i} 个选项图被误删"
    assert content.count("\\item") == 4


def test_strip_image_reference_bare_item_does_not_keep_trailing_plain_image():
    r"""裸 `\item` 判据不能外溢：**带内容**的 `\item` 行后面的普通配图照旧剥离。

    第 5 题的形态是 `\item 106` / `\item 105` …（`\item` 后已有正文），
    末尾挂着一张普通配图。把 `\item` 行一律当作「选项标号行」会把这张图
    误留在正文里；因此只认**裸** `\item` 行（整行只有 `\item`）。
    """

    from mathbank.parsing_pipeline import _strip_image_reference

    content = (
        "由表格可得回归方程为（ ）\n\n"
        "\\begin{choices}\n"
        "\\item 106\n\\item 105\n\\item 104\n\\item 103\n"
        "\\end{choices}\n\n"
        "![](/static/uploads/mineru_trailing.jpg)"
    )
    fixed = _strip_image_reference(content, "/static/uploads/mineru_trailing.jpg")
    assert "mineru_trailing.jpg" not in fixed


def _choice_urls(n, task="t"):
    return [f"/static/uploads/tmp/pdf_crop_{task}_0_{i}.png" for i in range(n)]


def test_backfill_choice_images_fills_bare_item_shells():
    r"""`\item A` 空壳选项要按顺序补回选项图（线上第 1 题实测的第三种 AI 形态）。

    选项本身是图形时，AI 有时只把图片列进 referenced_images，`\item` 行只剩
    剥离标号后残留的字母，题干成了「A/B/C/D」四个空壳。
    """

    from mathbank.parsing_pipeline import _backfill_choice_images

    urls = _choice_urls(4)
    content = "题干（ ）\n\n\\begin{choices}\n" + "".join(
        f"\\item {c}\n" for c in "ABCD"
    ) + "\\end{choices}"
    out, used = _backfill_choice_images(content, urls, "single_choice")
    assert len(used) == 4
    for i in range(4):
        assert f"pdf_crop_t_0_{i}.png" in out
    assert out.count("\\item") == 4


def test_backfill_choice_images_skips_when_images_already_referenced():
    r"""正文已有图片引用时**绝不**回填，否则同一张图会渲染两次。"""

    from mathbank.parsing_pipeline import _backfill_choice_images

    urls = _choice_urls(4)
    content = "题干（ ）\n\n\\begin{choices}\n" + "".join(
        f"\\item\n![插图]({u})\n" for u in urls
    ) + "\\end{choices}"
    out, used = _backfill_choice_images(content, urls, "single_choice")
    assert used == []
    assert out == content


def test_backfill_choice_images_ignores_non_choice_and_real_options():
    r"""非选择题、以及带真实内容的选项，都不许被回填改写。"""

    from mathbank.parsing_pipeline import _backfill_choice_images

    urls = _choice_urls(4)
    # 非选择题
    out, used = _backfill_choice_images("计算 $1+1$。", urls, "detailed_answer")
    assert used == []
    # 带真实内容的选项（`\item 106`）
    real = "题干（ ）\n\n\\begin{choices}\n\\item 106\n\\item 105\n\\item 104\n\\item 103\n\\end{choices}"
    out, used = _backfill_choice_images(real, urls, "single_choice")
    assert used == []
    assert out == real


def test_synthesize_image_choices_rebuilds_lost_option_block():
    r"""选项区**整体丢失**（连 choices 环境都没有）时按 2/4 张图重建。"""

    from mathbank.parsing_pipeline import _synthesize_image_choices

    urls = _choice_urls(4)
    content = "1. 下列各曲线中，不能表示 $y$ 是 $x$ 的函数的是（）"
    out, used = _synthesize_image_choices(content, urls, "single_choice")
    assert len(used) == 4
    assert "\\begin{choices}" in out and "\\end{choices}" in out
    for i in range(4):
        assert f"pdf_crop_t_0_{i}.png" in out


def test_synthesize_image_choices_refuses_odd_image_counts():
    r"""图片数量不是 2/4 时不猜——宁可留给人工核对，也不凭空造选项。"""

    from mathbank.parsing_pipeline import _synthesize_image_choices

    out, used = _synthesize_image_choices(
        "1. 如图，求阴影面积（）", _choice_urls(3), "single_choice"
    )
    assert used == []


def test_paper_list_present(client):
    r"""确保历史试卷库列表接口可用（清理 from-import 后保留的基础能力）。"""

    headers = {"X-Local-Token": LOCAL_TOKEN}
    res = client.get("/api/papers")
    assert res.status_code == 200


def test_circled_subitems_are_indented_one_level_deeper_in_latex():
    r"""圈号小小问（①②③…）在导出 LaTeX 时必须比一级小问 (1)/(2) 再深一层。

    一级小问靠 `\parindent` 拿到首行缩进（只作用于**首行**）；圈号若也走 `\parindent`
    就和一级**视觉同级**，成品卷面看不出层级（用户反馈「这不和常理」）。改用
    `\leftskip` 包成缩进块：它对段落**每一行**生效，折行不会退回左边界。
    """
    from mathbank.paper_helper import clean_content_for_latex

    stem = (
        "已知二次函数 $f(x)=ax^{2}+bx+c$。\n\n"
        "(1) 若 $a,b,c$ 满足 $a > b > c$。\n"
        "① 求证：两函数的图象交于不同两点 $A$，$B$。\n"
        "② 求线段 $AB$ 在 $x$ 轴上的射影 $A_{1}B_{1}$ 的长的取值范围。"
    )
    out = clean_content_for_latex(stem, "detailed_answer")
    assert out.count(r"{\leftskip=2\parindent\noindent") == 2
    assert r"{\leftskip=2\parindent\noindent ① 求证：" in out
    assert r"{\leftskip=2\parindent\noindent ② 求线段" in out
    # (1) 是一级小问，必须留在普通段落层级，不得被包进缩进组：
    # 第一个缩进组之前应当只有 (1) 那段、且不含任何圈号。
    head = out.split(r"{\leftskip")[0]
    assert "(1) 若" in head
    assert "①" not in head and "②" not in head

    # 解答侧原先只对 (1)/解：/证明：/因为… 升段，**圈号漏在候选之外** →
    # `（2）…\n① 证明：…` 在 LaTeX 里只是一个空格，①② 会被并进上一句。
    answer = "(2) 设 $a=c=t$。\n① 证明：$x_{1}$ 是根。\n② 求 $x_{3},x_{4}$ 的大小。"
    ans_out = clean_content_for_latex(answer, "detailed_answer", is_answer=True)
    assert ans_out.count(r"{\leftskip=2\parindent\noindent") == 2
    assert r"{\leftskip=2\parindent\noindent ① 证明：$x_{1}$ 是根。\par}" in ans_out
    assert r"{\leftskip=2\parindent\noindent ② 求 $x_{3},x_{4}$ 的大小。\par}" in ans_out

    # 缩进保持幂等：二次调用不得套第二层 \leftskip
    from mathbank.paper_helper import indent_circled_paragraphs

    assert indent_circled_paragraphs(out) == out
    assert indent_circled_paragraphs(ans_out) == ans_out


def test_circled_subitem_indent_leaves_plain_and_block_content_alone():
    r"""防误伤：只有一级小问、或圈号夹在句中时，都不得被包进缩进组。"""
    from mathbank.paper_helper import clean_content_for_latex

    # 只有一级小问 → 完全不该出现缩进组
    plain = "(1) 若 $a,b,c$ 满足 $a > b > c$。\n(2) 当 $a=c=t$ 时，求根。"
    assert r"\leftskip" not in clean_content_for_latex(plain, "detailed_answer")

    # 圈号夹在句子中间（不是行首）只是普通符号，不得被当小问拆段 + 缩进
    inline = "题中 ① 表示甲，② 表示乙。\n(1) 求甲与乙的关系。"
    inline_out = clean_content_for_latex(inline, "detailed_answer")
    assert r"\leftskip" not in inline_out
    assert "① 表示甲" in inline_out

    # 块级环境自成段落，不参与缩进（否则 \begin{...} 会被包进组里）
    block = "已知条件如下。\n\n\\begin{center}甲与乙\\end{center}"
    assert r"\leftskip" not in clean_content_for_latex(block, "detailed_answer")


# ---------------------------------------------------------------------------
# 整卷删除：题目一律保留 + usage_count 按现存绑定重算 + 删前强制完整备份
# ---------------------------------------------------------------------------


def _make_question(db_session, content="求 $f(x)=x^2$ 的最小值", **kw):
    q = Question(content=content, question_type="detailed_answer", difficulty="basic", **kw)
    db_session.add(q)
    db_session.flush()
    return q


def _save_paper(client, headers, title, q_ids, scores=None):
    payload = {
        "title": title,
        "subtitle": "",
        "paper_type": "exam",
        "total_score": sum(scores or [5] * len(q_ids)),
        "questions": [{"id": i, "score": (scores or [5] * len(q_ids))[n]} for n, i in enumerate(q_ids)],
    }
    res = client.post("/api/paper/save", json=payload, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["paper_id"]


@pytest.fixture
def stubbed_full_backup(monkeypatch, tmp_path):
    """把「删卷前建完整备份」这一步打桩，让删卷用例离开本机也能跑。

    create_full_backup 的默认库路径取项目根的 math_question_bank.db，而 .gitignore
    把 *.db 挡在库外：干净检出（或独立 worktree）里根本没有这个文件，删卷于是 500
    「删除前的完整备份未能创建，已中止删除」。另外在开发机上默认参数还会顺手把**真实
    题库**在线快照一遍，既慢又没必要。这几条用例守的是「题目与绑定不许被删掉」的数据
    不变式，备份机制本身由 test_delete_paper_backup_created_before_write /
    _aborts_when_backup_fails 专门覆盖，所以这里只把这一步换掉。
    """
    import mathbank.backup as backup_mod

    calls = []
    fake_zip = tmp_path / "mathbank-backup-stub.zip"

    def fake_create(**kwargs):
        calls.append(kwargs)
        fake_zip.write_bytes(b"PK\x03\x04stub")
        return fake_zip

    monkeypatch.setattr(backup_mod, "create_full_backup", fake_create)
    return calls


def test_delete_paper_keeps_every_question_and_its_links_of_other_papers(client, db_session, stubbed_full_backup):
    """核心不变式：删一份卷，**题目一行都不许少**，别的卷的绑定也不受影响。"""
    from main import LOCAL_TOKEN
    headers = {"X-Local-Token": LOCAL_TOKEN}
    qs = [_make_question(db_session, content=f"共享题 {i}") for i in range(3)]
    db_session.commit()
    ids = [q.id for q in qs]
    paper_a = _save_paper(client, headers, "待删除卷A", ids[:2])
    paper_b = _save_paper(client, headers, "保留卷B", ids[1:])
    db_session.commit()

    before_total = db_session.query(Question).count()
    res = client.delete(f"/api/papers/{paper_a}", headers=headers)
    assert res.status_code == 200, res.text
    assert len(stubbed_full_backup) == 1, "删卷前没有登记备份"
    db_session.expire_all()

    assert db_session.query(Question).count() == before_total, "删试卷把题目删掉了"
    assert all(db_session.get(Question, i) is not None for i in ids), "题目被级联删除"
    # A 卷绑定清零、B 卷绑定一根不少
    assert db_session.query(PaperQuestion).filter(PaperQuestion.paper_id == paper_a).count() == 0
    assert db_session.query(PaperQuestion).filter(PaperQuestion.paper_id == paper_b).count() == 2
    assert db_session.query(Paper).filter(Paper.id == paper_a).first() is None
    assert db_session.query(Paper).filter(Paper.id == paper_b).first() is not None


def test_delete_paper_recounts_usage_from_live_links_not_incremental_decrement(client, db_session, stubbed_full_backup):
    """usage_count 用「现存绑定数」绝对口径重算，而不是增量扣减。

    事故原型：save_paper 每次新建 Paper 并对每题 +1，同一份卷保存两次计数就虚高；
    增量扣减只会把这个漂移一直带下去。重算才是收敛的。
    """
    from main import LOCAL_TOKEN
    headers = {"X-Local-Token": LOCAL_TOKEN}
    shared = _make_question(db_session, content="两卷共用")
    dropped = _make_question(db_session, content="只在被删卷")
    db_session.commit()

    p1 = _save_paper(client, headers, "卷一", [shared.id, dropped.id])
    p2 = _save_paper(client, headers, "卷二", [shared.id])
    db_session.commit()

    # 人为制造漂移：把计数改成与真实绑定不符的值，重算必须把它拉回正确值
    shared.usage_count = 99
    dropped.usage_count = 99
    db_session.commit()

    res = client.delete(f"/api/papers/{p1}", headers=headers)
    assert res.status_code == 200, res.text
    assert len(stubbed_full_backup) == 1, "删卷前没有登记备份"
    db_session.expire_all()

    assert db_session.get(Question, shared.id).usage_count == 1, "共用题应剩 1 份现存绑定"
    assert db_session.get(Question, dropped.id).usage_count == 0, "独占题应归零"


def test_delete_paper_backup_created_before_write(client, db_session, monkeypatch, tmp_path):
    """删前强制登记完整备份：备份落盘成功才允许动数据。"""
    from main import LOCAL_TOKEN
    import mathbank.backup as backup_mod

    headers = {"X-Local-Token": LOCAL_TOKEN}
    q = _make_question(db_session, content="备份校验题")
    db_session.commit()
    pid = _save_paper(client, headers, "备份验证卷", [q.id])
    db_session.commit()

    fake_zip = tmp_path / "mathbank-backup-fake.zip"
    calls = []

    def fake_create(**kw):
        calls.append(kw)
        fake_zip.write_bytes(b"PK\x03\x04fake")
        return fake_zip

    monkeypatch.setattr(backup_mod, "create_full_backup", fake_create)
    res = client.delete(f"/api/papers/{pid}", headers=headers)
    assert res.status_code == 200, res.text
    assert len(calls) == 1, "删除前没有创建完整备份"
    assert fake_zip.name in res.json().get("backup", ""), "响应没告知用户备份文件名"


def test_delete_paper_aborts_when_backup_fails(client, db_session, monkeypatch):
    """反向验证（安全网的核心）：备份失败必须**拒绝删除**，数据一行未动。

    若这条写成「备份失败也照删」，用户误删就没有任何恢复路径了——所以它必须红。
    """
    from main import LOCAL_TOKEN
    import mathbank.backup as backup_mod

    headers = {"X-Local-Token": LOCAL_TOKEN}
    q = _make_question(db_session, content="备份失败不该被牵连的题")
    db_session.commit()
    pid = _save_paper(client, headers, "备份失败卷", [q.id])
    db_session.commit()

    def boom(**kw):
        raise RuntimeError("磁盘满了")

    monkeypatch.setattr(backup_mod, "create_full_backup", boom)
    res = client.delete(f"/api/papers/{pid}", headers=headers)
    assert res.status_code == 500
    assert "备份" in res.json()["message"]
    db_session.expire_all()
    assert db_session.query(Paper).filter(Paper.id == pid).first() is not None, "备份失败却删掉了试卷"
    assert db_session.query(PaperQuestion).filter(PaperQuestion.paper_id == pid).count() == 1, "备份失败却解绑了题目"


def test_delete_paper_preview_reports_title_and_question_count(client, db_session):
    """预检接口只读：给确认弹窗提供「含 N 题」的真实数字，且不留任何写入痕迹。"""
    from main import LOCAL_TOKEN
    headers = {"X-Local-Token": LOCAL_TOKEN}
    qs = [_make_question(db_session, content=f"预检题 {i}") for i in range(4)]
    db_session.commit()
    pid = _save_paper(client, headers, "预检目标卷", [q.id for q in qs])
    db_session.commit()

    papers_before = db_session.query(Paper).count()
    links_before = db_session.query(PaperQuestion).count()
    res = client.get(f"/api/papers/{pid}/delete-preview")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["paper_name"] == "预检目标卷"
    assert data["question_count"] == 4
    db_session.expire_all()
    assert db_session.query(Paper).count() == papers_before, "预检不该写库"
    assert db_session.query(PaperQuestion).count() == links_before, "预检不该写库"


def test_delete_paper_preview_missing_paper_is_404(client):
    from main import LOCAL_TOKEN
    res = client.get("/api/papers/999999/delete-preview", headers={"X-Local-Token": LOCAL_TOKEN})
    assert res.status_code == 404


# ===========================================================================
# 导出缺图事故回归锁（2026-10-18：预览能看、导出崩）
#
# 现场：题库 id=15 导出时 XeLaTeX 报
#   ! Unable to load picture or PDF file 'mineru_1790251898_1.jpg'.
#   l.149 \end
# 四张选项图其实都在 static/uploads 里、且是可解码的合法 JPEG。真正的原因是
# 导出资源清单的口径错了：collect_referenced_images 吃的清单来自
# to_dict()["image_paths"] = display_image_paths（UI 语义：已重绘成 TikZ 的原
# 参考图整批藏掉），而它自己又只认 Markdown 图 —— choices 里的选项图是
# \includegraphics 形态，restore_storage_images_to_markdown 刻意不还原它。
# 清单漏图 → 编译目录里没图 → XeLaTeX 撞死链 + -halt-on-error → 整卷出不了 PDF。
# ===========================================================================

_PROBE_STEM = r"设 $abc>0$，二次函数 $f(x)=ax^2+bx+c$ 的图象可能是 \paren"


def _probe_choice_content(names):
    """造一道「四个选项各挂一张图」的选择题（与题库 id=15 同形态）。"""
    items = "".join(
        "\\item \\includegraphics[max width=0.42\\linewidth,"
        "max height=3.2cm,keepaspectratio]{%s}\n" % name
        for name in names
    )
    return "%s\n\\begin{choices}\n    %s  \\end{choices}" % (_PROBE_STEM, items)


def _probe_question(names, display_paths, assets, legacy_reference=""):
    return {
        "id": 15,
        "question_type": "single_choice",
        "content": _probe_choice_content(names),
        "answer_markdown": "【答案】D",
        "image_paths": display_paths,
        "content_tikz_assets": assets,
        "tikz_reference_image_path": legacy_reference,
    }


def test_export_manifest_collects_choice_images_hidden_from_display(tmp_path):
    """选项图必须进导出清单：它既不在 display 口径里，也不是 Markdown 形态。"""
    from mathbank.paper_helper import collect_referenced_images

    jpgs = ["probe_a_1.jpg", "probe_a_2.jpg", "probe_a_3.jpg", "probe_a_5.jpg"]
    pngs = ["probe_tikz_1.png", "probe_tikz_2.png", "probe_tikz_3.png", "probe_tikz_4.png"]
    for name in jpgs + pngs:
        (tmp_path / name).write_bytes(b"probe image payload")

    assets = [
        {
            "id": "asset_%d" % index,
            "image_path": "/static/uploads/" + png,
            "reference_image_path": "/static/uploads/" + jpg,
            "tikz_code": r"\draw (0,0) -- (1,1);",
        }
        for index, (jpg, png) in enumerate(zip(jpgs, pngs), start=1)
    ]
    question = _probe_question(
        jpgs,
        # display 口径：原参考图已被 TikZ 取代，四张 jpg 全部不在这里
        ["/static/uploads/" + png for png in pngs],
        assets,
        legacy_reference="/static/uploads/" + jpgs[0],
    )
    data = [{"question": question, "score": 5}]

    # 1) 调用方没带 export 清单（老快照路径）：必须由 TikZ 资产字段自行还原
    collected = collect_referenced_images(data, str(tmp_path), "static/uploads")
    names = {os.path.basename(p) for p in collected}
    assert set(jpgs) <= names, "choices 里的选项图必须进导出清单，XeLaTeX 才不会撞死链"
    assert set(pngs) <= names

    # 2) 调用方带了 export 全量清单（组卷导出路径）：结果必须同样完整
    with_field = _probe_question(
        jpgs,
        ["/static/uploads/" + png for png in pngs],
        assets,
        legacy_reference="/static/uploads/" + jpgs[0],
    )
    with_field["export_image_paths"] = ["/static/uploads/" + n for n in jpgs + pngs]
    names2 = {os.path.basename(p) for p in collect_referenced_images([{"question": with_field, "score": 5}], str(tmp_path), "static/uploads")}
    assert set(jpgs) <= names2 and set(pngs) <= names2

    # 3) 扫描口径放宽不能顺手放宽安全边界：越界引用照旧被资产层挡掉
    evil = _probe_question(["../../.env"], [], [])
    assert collect_referenced_images([{"question": evil, "score": 5}], str(tmp_path), "static/uploads") == []


def test_export_manifest_keeps_chained_tikz_render_image(tmp_path):
    """TikZ 渲染图又被登记为下一次重绘的参考图（题库 id=7 形态）：清单不能藏它。"""
    from mathbank.paper_helper import collect_referenced_images

    render_name = "probe_chain_render.png"
    (tmp_path / render_name).write_bytes(b"probe render payload")
    # 正文直接引用渲染图本身，而它恰好也是某条资产的 reference_image_path
    assets = [{
        "id": "asset_chain",
        "image_path": "/static/uploads/probe_chain_next.png",
        "reference_image_path": "/static/uploads/" + render_name,
        "tikz_code": r"\draw (0,0) circle (1);",
    }]
    question = _probe_question([render_name], [], assets)
    collected = {
        os.path.basename(p)
        for p in collect_referenced_images([{"question": question, "score": 5}], str(tmp_path), "static/uploads")
    }
    assert render_name in collected, "被登记成参考图的渲染图仍是正文要排的文件，不能从清单里消失"


def test_missing_choice_image_degrades_to_red_placeholder(client, db_session):
    """图真不在磁盘上时：卷面降级成红字占位，导出照旧成功，并把缺图上报给前端。"""
    from urllib.parse import unquote
    from io import BytesIO
    from zipfile import ZipFile

    from main import LOCAL_TOKEN
    from mathbank.database import Question

    gone = ["probe_gone_1.jpg", "probe_gone_2.jpg"]
    row = Question(
        content=_probe_choice_content(gone),
        question_type="single_choice",
        answer_markdown="【答案】A",
        category_compulsory="必修第一册",
        difficulty="L3",
        source="缺图回归",
    )
    row._image_paths = json.dumps([])
    row.tikz_code = ""
    db_session.add(row)
    db_session.flush()

    headers = {"X-Local-Token": LOCAL_TOKEN}
    payload = {"title": "缺图导出回归", "subtitle": "数学", "paper_type": "exam",
               "questions": [{"id": row.id, "score": 5}]}

    captured = {}

    def fake_compile(tex, image_paths):
        captured["tex"] = tex
        captured["image_paths"] = list(image_paths)
        return b"%PDF-1.7\n% deterministic missing-image payload", ""

    with patch("main.compile_tex_to_pdf", side_effect=fake_compile):
        res = client.post("/api/paper/export/pdf", json=payload, headers=headers)
    assert res.status_code == 200, res.text

    tex = captured["tex"]
    assert r"\includegraphics" not in tex.split(r"\begin{document}")[-1], "死链绝不允许留在送进编译器的正文里"
    for name in gone:
        # 下划线必须转义：否则红字占位自己就会让 XeLaTeX 报 Missing $ inserted
        assert r"\textcolor{red}{【图片缺失：%s】}" % name.replace("_", r"\_") in tex
    assert res.headers["X-Paper-Missing-Images"] == "2"
    assert set(unquote(res.headers["X-Paper-Missing-Image-Files"]).split(",")) == set(gone)

    # LaTeX 源码包同样要自洽：.tex 里没有死链，images/ 里也没有凭空多出的空位
    zip_res = client.post("/api/paper/export/tex", json=payload, headers=headers)
    assert zip_res.status_code == 200
    assert zip_res.headers["X-Paper-Missing-Images"] == "2"
    with ZipFile(BytesIO(zip_res.content)) as archive:
        shipped = archive.read("试卷正文.tex").decode("utf-8")
        image_entries = [n for n in archive.namelist() if n.startswith("images/")]
    assert r"\includegraphics" not in shipped.split(r"\begin{document}")[-1]
    assert r"\textcolor{red}{【图片缺失：probe\_gone\_1.jpg】}" in shipped
    assert not image_entries


def test_display_image_paths_semantics_unchanged_for_ui():
    """UI 口径不许被导出修复带偏：to_dict 仍藏参考图，新口径只由导出链路显式带上。"""
    from mathbank.database import Question

    jpgs = ["/static/uploads/probe_u_%d.jpg" % i for i in range(1, 5)]
    pngs = ["/static/uploads/probe_u_tikz_%d.png" % i for i in range(1, 3)]
    row = Question(id=15, question_type="single_choice", content="占位题干")
    row._image_paths = json.dumps(jpgs + pngs)
    row._content_tikz_assets = json.dumps([
        {"id": "a1", "image_path": pngs[0], "reference_image_path": jpgs[0], "tikz_code": "\\draw (0,0);"},
        {"id": "a2", "image_path": pngs[1], "reference_image_path": jpgs[1], "tikz_code": "\\draw (0,0);"},
    ])
    row._answer_tikz_assets = json.dumps([])
    row.tikz_reference_image_path = jpgs[2]

    assert row.display_image_paths == [jpgs[3]] + pngs, (
        "已重绘成 TikZ 的原参考图（含遗留的 tikz_reference_image_path）"
        "仍不该出现在题目面板里"
    )
    assert row.export_image_paths == jpgs + pngs, "导出清单必须按物理口径一张不落"
    payload = row.to_dict()
    assert payload["image_paths"] == [jpgs[3]] + pngs, (
        "前端拿到的 image_paths 语义不能变"
    )
    assert "export_image_paths" not in payload, "导出清单由 _build_paper_questions_data 显式附加，不进 API 契约"


def test_dead_choice_images_still_compile_to_pdf():
    """库里的图全丢也必须出得了 PDF：卷面红字写明缺哪张，而不是整卷编译失败。"""
    import shutil

    import pymupdf

    from mathbank.paper_helper import (
        build_latex_document,
        compile_tex_to_pdf,
        patch_missing_latex_images,
    )

    if shutil.which("xelatex") is None:
        pytest.skip("本机无 XeLaTeX")

    gone = ["probe_real_%d.jpg" % i for i in range(1, 5)]
    question = _probe_question(gone, [], [])
    tex = build_latex_document("缺图真编译回归", "数学", "exam", [{"question": question, "score": 5}], include_answers=False)

    patched, missing = patch_missing_latex_images(tex, [])
    assert set(missing) == set(gone)
    assert r"\includegraphics" not in patched.split(r"\begin{document}")[-1]

    # 再喂一次：改写后的正文不含死链，patch 必须幂等
    again, missing_again = patch_missing_latex_images(patched, [])
    assert again == patched and missing_again == []

    pdf, log = compile_tex_to_pdf(patched, [])
    assert pdf, "含死图引用的试卷必须仍能编译：%s" % str(log)[-500:]
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    text = "".join(page.get_text() for page in doc)
    assert "图片缺失" in text
