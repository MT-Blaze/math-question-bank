# -*- coding: utf-8 -*-
"""裸公式后置修补（math_markdown）与 TikZ 导出占位符保护的回归测试。

这些行为来自上游 v2.2.x，属于「零风险移植」范畴：不触碰 schema、不改数据库，
只在文本层面做保守修补。
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from mathbank import parsing_pipeline
from mathbank.math_markdown import (
    compact_solution_markdown,
    normalize_question_math_markdown,
)
from mathbank.paper_helper import clean_content_for_latex


# --------------------------------------------------------------------------
# 1. normalize_question_math_markdown 的保守修补语义
# --------------------------------------------------------------------------

def test_wraps_naked_subscript_and_function_runs():
    """模型漏写定界符时，高置信度的裸公式应被补上 $...$。"""
    result = normalize_question_math_markdown("已知 x_1 与 f(x)=x_2 满足关系")
    assert "$x_1$" in result
    assert "$f(x)=x_2$" in result


def test_keeps_chinese_outside_delimiters():
    """中文必须留在数学定界符之外，不能整段被包进去。"""
    result = normalize_question_math_markdown("当 x \\geqslant 0 时")
    assert result == "当 $x \\geqslant 0$ 时"


def test_does_not_double_wrap_existing_math():
    """已有定界符的公式不得被重复包裹。"""
    source = "设 $y^2$ 与 $$\\frac{a}{b}$$ 均成立"
    assert normalize_question_math_markdown(source) == source


def test_preserves_markdown_images_and_lock_ids():
    """图片链接与公式锁定引用是协议标记，必须原样保留。"""
    source = "如图 ![](/static/uploads/abc.png) 且 [[MBM_abc_0001]] 成立"
    assert normalize_question_math_markdown(source) == source


def test_preserves_table_and_choices_environments():
    """表格与选项环境内部不得被插入 $，否则导出的 LaTeX 会直接编译失败。"""
    source = "\\begin{tabular}{cc} a & b \\\\ \\end{tabular}"
    assert normalize_question_math_markdown(source) == source
    choices = "\\begin{choices} \\item 1 \\item 2 \\end{choices}"
    assert normalize_question_math_markdown(choices) == choices


def test_preserves_illustration_box_marker():
    """OCR 协议标记里的下划线不能被当成上标处理。"""
    source = "[ILLUSTRATION_BOX: 12, 34, 56, 78]"
    assert normalize_question_math_markdown(source) == source


def test_page_marker_is_removed_without_dollar_pollution():
    r"""跨页页标必须被**整条删除**，且不能残留 `$` 污染。

    `<!-- MATHBANK_PDF_PAGE:3 -->` 里的连字符会被 _MATH_RUN_RE 当成数学减号，
    旧行为产出 `<!$-- MATHBANK_PDF_PAGE:3 -->$`：页标既没被清掉，又被塞进一对
    `$`（实测第 4 题题干就是这个形态，前端会原样显示）。提示词 4.1 明确要求
    页标不得出现在输出题干中，所以这里直接删掉整行。
    """
    source = "4. 某校举办了知识竞赛（ ）\n<!-- MATHBANK_PDF_PAGE:3 -->\n\\begin{choices}\n\\item 中位数 70\n\\end{choices}"
    out = normalize_question_math_markdown(source)
    assert "MATHBANK_PDF_PAGE" not in out
    assert "$" not in out
    assert "4. 某校举办了知识竞赛（ ）" in out
    assert "\\begin{choices}" in out
    assert "\\item 中位数 70" in out


def test_page_marker_legacy_dollar_variant_is_healed():
    """历史上被 `$` 污染的页标变体也要能清掉（老数据重新保存时自愈）。"""
    source = "题干\n<!$-- MATHBANK_PDF_PAGE:3 -->$\n后续文字"
    out = normalize_question_math_markdown(source)
    assert "MATHBANK_PDF_PAGE" not in out
    assert "$" not in out
    assert "题干" in out and "后续文字" in out


def test_html_comment_is_preserved_from_math_wrapping():
    """普通 HTML 注释里的连字符不该被包成公式。"""
    source = "说明 <!-- a-b --> 结束"
    assert normalize_question_math_markdown(source) == source


def test_empty_and_non_string_passthrough():
    assert normalize_question_math_markdown("") == ""
    assert normalize_question_math_markdown(None) == ""


def test_structural_commands_are_not_math():
    """纯结构性命令（\\item / \\fillin）不构成数学串，不应被包裹。"""
    assert normalize_question_math_markdown("\\item 第一小问") == "\\item 第一小问"


def test_literal_backslash_n_before_uppercase_is_restored_as_newline():
    r"""MinerU 常把换行写成字面 `\n`；`\nA` 绝不能被当成 LaTeX 命令包进 `$...$`。

    真实回归（MinerU 云端路线第 1 题）：AI 把四个选项写成 `\nA \nB \nC \nD`，
    补定界符逻辑把 `\nA` 里的 `nA` 认成控制词，整段被包成 `$\nA$`，题干变成
    `（ ）$\nA \n$$\nB \n$…` —— 选项全毁、且留下大量孤立 `$`。
    """
    source = "下列各曲线中，不能表示y是x的函数的是（ ）" + r"\nA \nB \nC \nD "
    out = normalize_question_math_markdown(source)
    assert "\\n" not in out
    assert "$" not in out
    assert out == "下列各曲线中，不能表示y是x的函数的是（ ）\nA \nB \nC \nD "


def test_literal_backslash_n_before_lowercase_command_is_preserved():
    r"""`\n` 后面跟小写字母时可能是真 LaTeX 命令（\nabla / \neq / \nu / \not），必须保留。"""
    for command in (r"\nabla", r"\neq", r"\nu", r"\not"):
        source = "已知 " + command + " 成立"
        assert command in normalize_question_math_markdown(source)


def test_literal_backslash_n_before_digit_or_punctuation_is_restored():
    r"""数字 / 标点 / 空白前的 `\n` 不可能是控制词，一律还原成换行。"""
    assert normalize_question_math_markdown(r"第一行\n1 第二行") == "第一行\n1 第二行"
    assert normalize_question_math_markdown(r"第一行\n，第二行") == "第一行\n，第二行"


def test_replaces_multiplication_star_with_cdot_in_math():
    """数学环境内的乘法 * 应规范化为 \\cdot。"""
    assert normalize_question_math_markdown("$a*x+b$") == "$a\\cdot x+b$"
    assert normalize_question_math_markdown("$$f(x)=a*x$$") == "$$f(x)=a\\cdot x$$"


def test_preserves_star_outside_math_and_in_superscript():
    """普通文本中的 * 和上标/下标中的 * 不应被替换。"""
    source = "选项 A*B 成立，且 $x^*$ 是共轭"
    assert normalize_question_math_markdown(source) == source


def test_converts_underscore_blank_to_fillin():
    """OCR/Word 导出的连续下划线填空占位符应规范化为 \\fillin。"""
    assert normalize_question_math_markdown("这样的函数有 ___ 个") == "这样的函数有 \\fillin 个"
    assert normalize_question_math_markdown("最小值为_____") == "最小值为\\fillin"
    # 数学环境内的下划线是下标，不应转换
    assert normalize_question_math_markdown("$a_1 + a_{23} = ___$") == "$a_1 + a_{23} = ___$"
    # 已是 LaTeX 转义的下划线也不应转换
    assert normalize_question_math_markdown(r"路径 \\_\\_\\_foo") == r"路径 \\_\\_\\_foo"


# --------------------------------------------------------------------------
# 2. 批量拆题落库前的接线
# --------------------------------------------------------------------------

def test_parse_paper_text_internal_defers_repair_to_the_storage_chain():
    """拆题阶段只做静默净化；补定界符由入库归一化链唯一负责。

    历史上这里先跑字符层、写库时只跑结构层，导致「手敲题」与「拆卷题」的同一种
    写法进库变成两种源码，并且字符层先跑会把管道表格行包成 `$| a | b |$` 让结构层
    认不出表格而丢行。现在这条链只在真正落地时跑一次（post_process 末段 + 写库边界）。
    """
    payload = {
        "questions": [
            {
                "content": "已知 x_1 与 x_2 是方程的两根",
                "answer_markdown": "[EXTRACTED_ORIGINAL]由 \\alpha 可得结论",
            }
        ]
    }
    result = _parse_with_mock_payload(payload)

    assert len(result) == 1
    # 静默净化仍然在这一层完成
    assert "[EXTRACTED_ORIGINAL]" not in result[0]["answer_markdown"]
    # 但拆题阶段不再抢跑字符层
    assert "$x_1$" not in result[0]["content"]
    assert "$\\alpha$" not in result[0]["answer_markdown"]

    # 落地时由唯一入口补齐：三条录入路径共用同一条链
    from mathbank.latex_normalize import normalize_for_storage

    stored_content = normalize_for_storage(result[0]["content"], "detailed_answer")
    stored_answer = normalize_for_storage(result[0]["answer_markdown"], is_answer=True)
    assert "$x_1$" in stored_content and "$x_2$" in stored_content
    assert "$\\alpha$" in stored_answer


def test_every_import_path_wires_the_repair_helper():
    """三个录入通道都必须接上修补，漏一个就会出现裸公式。"""
    from mathbank import routes_ai, routes_paper

    for module in (parsing_pipeline, routes_ai, routes_paper):
        assert hasattr(module, "normalize_question_math_markdown")


# --------------------------------------------------------------------------
# 3. TikZ 导出占位符保护
# --------------------------------------------------------------------------

def test_tikz_code_survives_paragraph_joining():
    """TikZ 里的行尾 % 注释不得吞掉紧随其后的绘图代码。"""
    source = (
        "题干说明：\n"
        "\\begin{tikzpicture}\n"
        "% 底边\n"
        "\\draw (0,0) -- (2,0);\n"
        "% 高\n"
        "\\draw (0,0) -- (0,1);\n"
        "\\end{tikzpicture}\n"
        "求三角形面积。"
    )
    result = clean_content_for_latex(source, q_type="detailed_answer")
    assert result.count("\\draw") == 2
    assert "\\begin{tikzpicture}" in result
    assert "\\end{tikzpicture}" in result
    assert "% 底边" in result
    # 注释与其后的绘图代码之间必须还隔着换行，不能被拼成同一行
    assert "% 底边\n\\draw" in result


# --------------------------------------------------------------------------
# 4. compact_solution_markdown：解析排版紧凑化
# --------------------------------------------------------------------------

def test_compact_solution_downgrades_single_line_display_math():
    """单行 $$...$$ 应降级为行内 $...$，避免前端渲染成居中独立行。"""
    assert compact_solution_markdown("$$x=1$$") == "$x=1$"
    source = "由 $$f'(x)=0$$ 得 $$x=1$$"
    assert compact_solution_markdown(source) == "由 $f'(x)=0$ 得 $x=1$"


def test_compact_solution_keeps_environment_display_math():
    """含数学环境或 `\\` 换行的真正行间公式必须原样保留。"""
    aligned = "$$\\begin{aligned} a &= 1 \\\\ b &= 2 \\end{aligned}$$"
    assert compact_solution_markdown(aligned) == aligned
    tabular = "$$\\begin{tabular}{cc} a & b \\end{tabular}$$"
    assert compact_solution_markdown(tabular) == tabular
    # 含 `\\` 换行的跨行块同样是真正的多行公式，原样保留
    explicit_break = "$$a = 1 \\\\ b = 2$$"
    assert compact_solution_markdown(explicit_break) == explicit_break


def test_compact_solution_downgrades_blocks_written_across_lines():
    """跨行书写的普通 $$ 块也要降级——这是「有些式子换行、有些不换行」的根因。

    KaTeX 在 display 模式下本来就把裸换行当空白，所以压平换行不改变公式语义，
    只是把块级公式换成行内公式，让它与前后文字排在同一行。旧实现只降级单行块，
    导致同一份解析里简单式子被压平、跨行式子仍独立成行。
    """
    assert compact_solution_markdown("$$\na + b = c\n$$") == "$a + b = c$"
    # 缩进与制表符不得残留
    assert compact_solution_markdown("$$\n\tk = 2 \n$$") == "$k = 2$"
    assert compact_solution_markdown("$$\n\n  m = 3  \n\n$$") == "$m = 3$"
    # 前后有空行的跨行块：段落结构保留，但块内换行被压平
    source = "【解析】\n\n$$\n\ta + 1 = 2\n$$\n\n故答案为 D。"
    compacted = compact_solution_markdown(source)
    assert "$$" not in compacted
    assert "$a + 1 = 2$" in compacted
    assert compacted.count("\n\n") == 2


def test_compact_solution_collapses_excess_blank_lines():
    """三段以上连续换行压缩为一个空行，段落分隔本身保留。"""
    assert compact_solution_markdown("第一段\n\n\n\n第二段") == "第一段\n\n第二段"
    # 正常的段落分隔不受影响
    assert compact_solution_markdown("第一段\n\n第二段") == "第一段\n\n第二段"


def test_compact_solution_preserves_code_spans():
    """行内代码与围栏代码块内的 $$ 不得被降级。"""
    assert compact_solution_markdown("`$$x$$`") == "`$$x$$`"
    fenced = "```\n$$x$$\n```"
    assert compact_solution_markdown(fenced) == fenced


def test_compact_solution_safe_edge_cases():
    """空值、含内层 $ 的畸形块、题目插图标记都不应被破坏。"""
    assert compact_solution_markdown("") == ""
    assert compact_solution_markdown(None) == ""
    assert compact_solution_markdown("$$a$b$$") == "$$a$b$$"
    image = "![](/static/uploads/a.png)"
    assert compact_solution_markdown(image) == image


def test_every_import_path_wires_the_compaction_helper():
    """三个录入通道 + 单题解析都必须接上紧凑化，漏一个就会重新逐式独立成行。"""
    from mathbank import routes_ai, routes_paper

    for module in (parsing_pipeline, routes_ai, routes_paper):
        assert hasattr(module, "compact_solution_markdown")


def _parse_with_mock_payload(payload):
    """让 parse_paper_text_internal 返回指定 JSON，绕开真实模型调用。"""
    response = MagicMock()
    response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]
    }
    provider = SimpleNamespace(
        api_key="test-key",
        api_base="https://example.invalid",
        model_name="mock-model",
        provider_label="Mock",
        credential_label="Mock Key",
        reasoning_effort=None,
        provider_code="mock",
    )
    with patch.object(parsing_pipeline, "resolve_text_provider", return_value=provider), \
         patch.object(parsing_pipeline, "post_chat_completion", return_value=response), \
         patch.object(parsing_pipeline, "get_metadata_cache", return_value={}), \
         patch.object(parsing_pipeline, "get_current_curriculum", return_value={}), \
         patch.object(parsing_pipeline, "build_pdf_parse_system_prompt", return_value="sys"):
        return parsing_pipeline.parse_paper_text_internal("原始试卷文本", True)


def test_parse_paper_text_internal_compacts_answer_only_at_storage_time():
    """整卷导入后 answer_markdown 的 $$ 必须降级，而题干的独立公式保持原样。

    降级动作归属于入库归一化链（而不是拆题阶段），断言口径不变。
    """
    from mathbank.latex_normalize import normalize_for_storage

    payload = {
        "questions": [
            {
                "content": "已知函数 $$f(x)=x^2$$ 的图像过原点",
                "answer_markdown": "由 $$f'(x)=0$$ 得 $$x=0$$",
            }
        ]
    }
    result = _parse_with_mock_payload(payload)
    assert len(result) == 1
    # 拆题阶段原样交回，不抢跑
    assert "$$f'(x)=0$$" in result[0]["answer_markdown"]

    stored_content = normalize_for_storage(result[0]["content"], "detailed_answer")
    stored_answer = normalize_for_storage(result[0]["answer_markdown"], is_answer=True)
    # 解析正文里的独立公式被降级为行内公式
    assert "$f'(x)=0$" in stored_answer and "$x=0$" in stored_answer
    assert "$$" not in stored_answer
    # 题干的独立公式属于正常排版，不得被降级
    assert "$$f(x)=x^2$$" in stored_content


def test_tikz_content_still_drops_duplicate_bitmap_images():
    """有 TikZ 时仍要剔除 markdown 图片，避免位图与矢量图重复渲染。"""
    source = (
        "\\begin{tikzpicture}\n\\draw (0,0) -- (1,1);\n\\end{tikzpicture}\n"
        "![](/static/uploads/dup.png)"
    )
    result = clean_content_for_latex(source, q_type="detailed_answer")
    assert "includegraphics" not in result
    assert "\\draw" in result


def test_plain_content_is_untouched_by_token_protection():
    """没有 TikZ 时，占位符机制不得改变任何既有输出。"""
    source = "已知 $x>0$，求 $x^2$ 的最小值。"
    assert clean_content_for_latex(source) == source
