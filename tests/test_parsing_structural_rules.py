"""入库前结构性清洗的回归测试（跨字段：题干 / 解析 / 题型 / 配图）。

被测入口是 :func:`mathbank.parsing_pipeline.apply_storage_normalization` ——
三条录入路线（MinerU / 原生文字 / 视觉 OCR / TeX / 批量图片 / Word）共同的入库前最后一站，
所以「卡片预览 == 写库结果」这条不变量要求这几条规则也只能落在这里。

钉住的几件事：
- 题干吃进解析板块（【分析】/【详解】/【解析】/【解答】）→ 整块归位到解析区；
- 横排 ABCD 选项粘在题干末尾 → 题型修正 + 交给唯一的结构层包 ``choices``；
- 答案是多个独立字母 → 强制 ``multi_choice``（防「ACD 配单选题」）；
- 同一张图在解析里引用两次 → 只保留最后一处。
- 规则打架的顺序咬合：截断必须早于去重，搬回的解析落在【答案】之后 —— 否则内容会掉进截断线以下凭空消失。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mathbank.parsing_pipeline import (
    _answer_letters_from_markdown,
    _stem_option_letters,
    apply_storage_normalization,
)


def _card(content, answer="", question_type="single_choice", **extra):
    card = {
        "content": content,
        "answer_markdown": answer,
        "question_type": question_type,
        "image_paths": [],
    }
    card.update(extra)
    return card


def _run(cards):
    """跑一遍入库前清洗，并断言**再跑一遍字节不变**（幂等是所有入库规则的前提）。"""
    once = apply_storage_normalization([dict(card) for card in cards])
    twice = apply_storage_normalization([dict(card) for card in once])
    for a, b in zip(once, twice):
        assert a["content"] == b["content"], (a["content"], b["content"])
        assert a["answer_markdown"] == b["answer_markdown"], (
            a["answer_markdown"], b["answer_markdown"])
        assert a["question_type"] == b["question_type"]
    return once


STEM_WITH_CHOICES = (
    "已知集合 $M=\\{-1,1,2,4\\}$，$N=\\{1,2,4,16\\}$，能构成从 $M$ 到 $N$ 的函数的是 \\paren\n"
    "\\begin{choices}\n    \\item $y=2x$\n    \\item $y=x+2$\n"
    "    \\item $y=2^{|x|}$\n    \\item $y=2^x$\n\\end{choices}"
)


def test_analysis_block_in_stem_is_moved_into_solution():
    r"""题干末尾挂着【分析】/【详解】（2024 大庆卷实测形态）→ 整块搬进解析区，选项留在题干。"""
    content = STEM_WITH_CHOICES + "\n\n【分析】利用函数的定义，逐项判断即可.\n\n【详解】对于 A，$-1$ 无对应，A 不是."
    (card,) = _run([_card(content, "")])
    assert "【分析】" not in card["content"] and "【详解】" not in card["content"], card["content"]
    assert "利用函数的定义" in card["answer_markdown"], card["answer_markdown"]
    assert "对于 A" in card["answer_markdown"], card["answer_markdown"]
    # 题干只剩「正文 + choices 环境」，环境必须完整
    assert card["content"].count(r"\begin{choices}") == 1, card["content"]
    assert card["content"].count(r"\end{choices}") == 1, card["content"]
    assert r"\item $y=2^x$" in card["content"], card["content"]
    # 层级扁平化同一条链继续生效：最外层只有一个【解析】
    assert card["answer_markdown"].count("【解析】") == 1, card["answer_markdown"]
    assert "【分析】" not in card["answer_markdown"], card["answer_markdown"]


def test_inline_options_after_analysis_stay_in_stem():
    r"""「题干…【分析】… A．1 B．2 C．3 D．4」同一行粘连：解析归解析，选项必须回题干。"""
    content = (
        "设 $f(x)=\\frac{5}{2}-\\frac{1}{2^x+1}$，正实数 $a,b$ 满足 $f(a)+f(b-1)=4$，"
        "则最小值为【分析】由题设可得 $a+b=1$ A．$\\frac{4}{15}$ B．$\\frac{1}{4}$ "
        "C．$\\frac{1}{2}$ D．1"
    )
    (card,) = _run([_card(content, "【答案】D", "single_choice")])
    assert "【分析】" not in card["content"], card["content"]
    assert "由题设可得" in card["answer_markdown"], card["answer_markdown"]
    assert r"\begin{choices}" in card["content"], card["content"]
    # 入库链不再改写源码：行内分式保持 \frac（displaystyle 由渲染层注入）。
    for option in (r"$\frac{4}{15}$", r"$\frac{1}{2}$"):
        assert option in card["content"], (option, card["content"])
    assert card["content"].count(r"\item") == 4, card["content"]


def test_stem_that_is_pure_solution_is_left_alone():
    """整张卡从头就是解析（没有题干）时不改写：那是空壳题，不该被静默拆成无题干题。"""
    content = "【分析】由基本不等式可得最小值.\n\n【详解】$a+b=1$ ，故最小值为 $\\frac{1}{4}$ ."
    (card,) = _run([_card(content, "【答案】B", "detailed_answer")])
    assert card["content"].startswith("【解析】") or "由基本不等式" in card["content"], card["content"]


def test_leak_inside_choices_env_still_closes_the_environment():
    r"""解析标题挤在选项行里时，题干侧必须补 ``\end{choices}``，绝不留半截环境。"""
    content = (
        "则下列结论正确的是\n\\begin{choices}\n    \\item 1\n    \\item 2\n"
        "    \\item 【解析】由题意逐项判断即可.\n\\end{choices}"
    )
    (card,) = _run([_card(content, "【答案】B", "single_choice")])
    assert "【解析】" not in card["content"], card["content"]
    assert card["content"].count(r"\begin{choices}") == card["content"].count(r"\end{choices}"), card["content"]
    assert "由题意逐项判断即可" in card["answer_markdown"], card["answer_markdown"]


def test_inline_option_run_forces_choice_type_and_choices_env():
    r"""横排选项粘在题干末尾、题型却被判成填空题：题型修正 + 选项包进 choices（Bug 7）。"""
    content = (
        "已知 $a>0$，则 A．$a>1$ B．$a<1$ C．$a>1$ 或 $a<-1$ D．$a<-1$"
    )
    assert _stem_option_letters(content) == ["A", "B", "C", "D"]
    (card,) = _run([_card(content, "【答案】B", "fill_in_blank")])
    assert card["question_type"] == "single_choice", card["question_type"]
    assert r"\begin{choices}" in card["content"], card["content"]
    assert card["content"].count(r"\item") == 4, card["content"]


def test_multi_letter_answer_forces_multi_choice():
    r"""答案是 ACD 这种多个独立字母 → 强制 multi_choice（Bug 6）。"""
    content = "下列结论正确的是\nA．1\nB．2\nC．3\nD．4"
    (card,) = _run([_card(content, "【答案】ACD\n\n【解析】略", "single_choice")])
    assert card["question_type"] == "multi_choice", card
    assert r"\begin{choices}" in card["content"], card["content"]

    for answer in ("【答案】A、C、D", "【答案】AC", "【答案】（A）（C）"):
        assert len(_answer_letters_from_markdown(answer)) >= 2, answer


def test_multi_choice_fallback_does_not_touch_non_choice_answers():
    r"""兜底只认「整段除了字母与分隔符什么都没有」：多小问答案与区间答案一律不改题型。"""
    for answer in ("【答案】（1）A（2）B", "【答案】见解析", "【答案】$[-1,1]$", "【答案】"):
        assert _answer_letters_from_markdown(answer) == "", answer

    fill_in = _card("设 $f(x)=x^2$，则 $f(1)=$ \\fillin ，$f(2)=$ \\fillin",
                    "【答案】A、B", "fill_in_blank")
    (card,) = _run([fill_in])
    assert card["question_type"] == "fill_in_blank", card


def test_same_figure_referenced_twice_in_solution_keeps_last_one():
    r"""同一张图在【分析】与【详解】里各出现一次 → 只保留最后一处（Bug 8）。"""
    url = "/static/uploads/mineru_7_3.jpg"
    answer = (
        "【答案】B\n\n【解析】\n\n【分析】见下图 ![插图](%s)\n\n"
        "【详解】由图可得 $S=2$ ." % url
    )
    (card,) = _run([_card("如图，求阴影面积。", answer, "single_choice")])
    assert card["answer_markdown"].count("mineru_7_3") == 1, card["answer_markdown"]
    assert "由图可得" in card["answer_markdown"], card["answer_markdown"]


def test_duplicate_figure_in_url_forms_is_deduped_by_filename():
    r"""同一文件的两种 URL 形态（整串 URL 与裸文件名）也算同一张图：按文件名收口。"""
    answer = (
        "【答案】B\n\n【解析】\n\n【分析】配图 ![](/static/uploads/mineru_9_1.jpg)\n\n"
        "【详解】$S=2$ .\n\n![](mineru_9_1.jpg)"
    )
    (card,) = _run([_card("求面积", answer, "single_choice")])
    assert card["answer_markdown"].count("mineru_9_1") == 1, card["answer_markdown"]
    # 删的是**整行图片引用**，那一行的正文标题一个字都不许少（扁平化后是 \textbf{分析：}）
    assert "配图" in card["answer_markdown"], card["answer_markdown"]
    assert r"\textbf{详解：}" in card["answer_markdown"], card["answer_markdown"]


def test_two_different_figures_in_solution_are_both_kept():
    """两张**不同**的配图各自有效，绝不因为「解析里图多」被删。"""
    answer = (
        "【答案】B\n\n【解析】\n\n【分析】![插图](/static/uploads/mineru_1_1.jpg)\n\n"
        "【详解】![插图](/static/uploads/mineru_1_2.jpg)"
    )
    (card,) = _run([_card("求面积", answer, "single_choice")])
    for name in ("mineru_1_1", "mineru_1_2"):
        assert card["answer_markdown"].count(name) == 1, (name, card["answer_markdown"])


def test_inline_figure_in_a_sentence_is_not_torn_apart():
    r"""重复引用嵌在句子里时只摘图片语法，正文一个字不动（行内图不许撕坏那句话）。"""
    answer = (
        "【答案】B\n\n【解析】如图 $1$ 所示 ![图](/static/uploads/mineru_2_1.jpg) 可得结论，"
        "再由 ![图](/static/uploads/mineru_2_1.jpg) 可推出 $S=2$ 。"
    )
    (card,) = _run([_card("求面积", answer, "single_choice")])
    assert card["answer_markdown"].count("mineru_2_1") == 1, card["answer_markdown"]
    assert "如图" in card["answer_markdown"] and "可得结论" in card["answer_markdown"], card["answer_markdown"]
    assert "可推出" in card["answer_markdown"], card["answer_markdown"]


def test_clean_cards_are_untouched():
    """干净卡片必须一字不改：这几条兜底不许有任何「路过就改一笔」的副作用。"""
    content = STEM_WITH_CHOICES
    answer = "【答案】C\n\n【解析】由 $y=2^{|x|}$ 逐项验证，故选 C。"
    (card,) = _run([_card(content, answer, "single_choice")])
    assert card["content"] == content, card["content"]
    assert card["answer_markdown"] == answer, card["answer_markdown"]
    assert card["question_type"] == "single_choice"


def test_leaked_analysis_survives_a_swallowed_section_heading():
    r"""规则打架的现场：题干搬来的【分析】落在越界大标题之后会被截断删掉，必须一个不丢。

    这是 Bug1（题干吃进解析）与 Bug2（解析吞下「三、填空题」）叠加的真实形态：
    搬走的正文如果追加到解析末尾，正好掉进截断线以下，整段凭空消失。
    """
    content = STEM_WITH_CHOICES + "\n\n【分析】利用基本不等式可求得最小值。"
    answer = "【答案】ACD\n\n【详解】把 $x=1$ 代入验证。\n\n三、填空题（每题 5 分，共 20 分）"
    (card,) = _run([_card(content, answer, "single_choice")])
    assert "利用基本不等式" in card["answer_markdown"], card["answer_markdown"]
    assert "代入验证" in card["answer_markdown"], card["answer_markdown"]
    assert "三、填空题" not in card["answer_markdown"], card["answer_markdown"]
    assert card["answer_markdown"].count("【解析】") == 1, card["answer_markdown"]
    assert card["answer_markdown"].startswith("【答案】ACD"), card["answer_markdown"]


def test_duplicate_figure_beyond_a_swallowed_heading_is_not_lost():
    r"""重复配图的「最后一处」落在越界区里时，必须保留界内那张，而不是整题无图。

    Bug8 的去重与 Bug2 的截断顺序颠倒过一次：先去重（保留最后一处）再截断，
    结果界内那张被删、界外那张被截，配图彻底消失。
    """
    answer = (
        "【答案】B\n\n【解析】如图 ![图](/static/uploads/mineru_9_1.jpg) 所示。\n\n"
        "三、填空题（每题 5 分，共 20 分）\n\n![图](/static/uploads/mineru_9_1.jpg)"
    )
    (card,) = _run([_card("求面积", answer, "single_choice")])
    assert "三、填空题" not in card["answer_markdown"], card["answer_markdown"]
    assert card["answer_markdown"].count("mineru_9_1") == 1, card["answer_markdown"]
