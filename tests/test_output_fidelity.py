# -*- coding: utf-8 -*-
r"""AI 拆题输出的可数不变量守恒校验。

与 `tests/test_content_locks.py` 互补：锁机制保证**公式原文**不丢，本模块保证
选项数、表格数、`\fillin` 个数与 `[插图待补]` 编号集不被静默改写。

⚠️ 本文件所有题目文本一律用 raw 字符串。写成普通字符串时 `\fillin` 里的 `\f` 是
换页符、`\begin` 里的 `\b` 是退格符，夹具会静默变形，测出来的「通过」毫无意义
（本项目踩过）。
"""

from mathbank.output_fidelity import (
    check_split_output_fidelity,
    format_fidelity_log,
)


CHOICE_STEM = (
    r"设集合 $A=\{1,2\}$，则 $A$ 的子集个数为" "\n"
    r"\begin{choices}" "\n"
    r"\item $2$" "\n"
    r"\item $4$" "\n"
    r"\item $8$" "\n"
    r"\item $16$" "\n"
    r"\end{choices}"
)

FILLIN_STEM = r"已知 $f(x)=x^2$，则 $f(2)=\fillin$，定义域为 $\fillin$。"

TABLE_BODY = (
    r"\begin{tabular}{|c|c|}" "\n"
    r"\hline" "\n"
    r"甲 & 乙 \\" "\n"
    r"\hline" "\n"
    r"1 & 2 \\" "\n"
    r"\hline" "\n"
    r"\end{tabular}"
)

SEPARATOR = "\n\n---\n\n"


def _q(content="", answer=""):
    return {"content": content, "answer_markdown": answer}


def test_identical_split_produces_no_flags():
    """正常拆题必须零告警——误报会把用户训练成无视角标。"""

    source = CHOICE_STEM + SEPARATOR + FILLIN_STEM + "\n" + TABLE_BODY
    questions = [_q(CHOICE_STEM), _q(FILLIN_STEM, TABLE_BODY)]
    report = check_split_output_fidelity(questions, source)

    assert report["mode"] == "per_question"
    assert report["checked"] == 2
    assert report["review_count"] == 0
    assert all("_fidelity_review" not in q for q in questions)
    assert "未发现异常" in format_fidelity_log(report)


def test_missing_option_is_flagged_with_counts():
    source = CHOICE_STEM
    trimmed = source.replace(r"\item $16$" "\n", "")
    assert trimmed != source, "夹具失效：替换没有命中"
    questions = [_q(trimmed)]
    report = check_split_output_fidelity(questions, source)

    reasons = questions[0]["_fidelity_review"]
    assert any("选项变少" in r and "4" in r and "3" in r for r in reasons), reasons
    assert report["review_count"] == 1


def test_choices_environment_collapsed_to_plain_text_reports_once():
    """模型把选项改写成 `A. B. C. D.` 纯文本时，环境与 item 同时归零。

    各报一条会把同一个问题拆成两个假问题，只保留「choices 环境丢失」这一条。
    """

    flattened = r"设集合 $A=\{1,2\}$，则 $A$ 的子集个数为 A. $2$  B. $4$  C. $8$  D. $16$"
    questions = [_q(flattened)]
    report = check_split_output_fidelity(questions, CHOICE_STEM)

    reasons = questions[0]["_fidelity_review"]
    assert len(reasons) == 1, reasons
    assert "选择题 choices 环境丢失" in reasons[0]


def test_lost_figure_placeholder_number_is_flagged():
    """占位符编号是硬证据：源里有图1、图2，输出只剩图1，说明图2 的位置被吞了。"""

    source = r"[插图待补: 图1] 第一题题干" + SEPARATOR + r"第二题 [插图待补: 图2]"
    questions = [_q(r"[插图待补: 图1] 第一题题干"), _q("第二题")]
    report = check_split_output_fidelity(questions, source)

    flagged = [q.get("_fidelity_review") for q in questions if q.get("_fidelity_review")]
    assert flagged, report
    assert any("插图占位符丢失" in r and "图2" in r for group in flagged for r in group), flagged


def test_hallucinated_figure_placeholder_is_flagged():
    source = "第一题题干没有任何图"
    questions = [_q("第一题题干没有任何图 " + r"[插图待补: 图7]")]
    check_split_output_fidelity(questions, source)

    reasons = questions[0]["_fidelity_review"]
    assert any("凭空多出" in r and "图7" in r for r in reasons), reasons


def test_unnumbered_placeholder_variants_do_not_mismatch():
    """无图号形态 `[插图待补]` 与带图号形态折叠后不得互相判成丢失/多出。"""

    source = "题干 " + r"[插图待补]" + " 结尾"
    questions = [_q("题干 " + r"[插图待补]" + " 结尾")]
    check_split_output_fidelity(questions, source)
    assert "_fidelity_review" not in questions[0]


def test_fillin_count_reduction_is_flagged():
    dropped = FILLIN_STEM.replace(r"$\fillin$", "")
    assert dropped.count(r"\fillin") == 1 and FILLIN_STEM.count(r"\fillin") == 2
    questions = [_q(dropped)]
    check_split_output_fidelity(questions, FILLIN_STEM)

    reasons = questions[0]["_fidelity_review"]
    assert any("填空下划线减少" in r and "2" in r and "1" in r for r in reasons), reasons


def test_table_loss_is_flagged():
    source = FILLIN_STEM + "\n" + TABLE_BODY
    questions = [_q(FILLIN_STEM)]  # 整张表被丢掉
    check_split_output_fidelity(questions, source)

    reasons = questions[0]["_fidelity_review"]
    assert any("表格数量减少" in r for r in reasons), reasons


def test_whitespace_and_line_break_differences_are_tolerated():
    """模型必然会在空格/换行上与源文有出入，锚点匹配必须扛得住。"""

    source = (
        "若 $a>b$，则下列不等式一定成立的是\n"
        "\\begin{choices}\n\\item $a+1>b+1$\n\\item $a-1>b-1$\n\\end{choices}"
    )
    reformatted = (
        "若 $a>b$，则下列不等式一定成立的是 \\begin{choices} "
        "\\item $a+1>b+1$ \\item $a-1>b-1$ \\end{choices}"
    )
    questions = [_q(reformatted)]
    report = check_split_output_fidelity(questions, source)

    assert report["checked"] == 1, report
    assert "_fidelity_review" not in questions[0], questions[0].get("_fidelity_review")


def test_regrouped_questions_still_detect_loss():
    """题目顺序变了也必须能定位并抓到丢项。

    源文里第 2 题的锚点在文档中位于第 1 题之后；卡片顺序颠倒时切片按各自起点切，
    仍然互不吞并对方的内容。
    """

    source = CHOICE_STEM + SEPARATOR + FILLIN_STEM
    questions = [
        _q(FILLIN_STEM),
        _q(CHOICE_STEM.replace(r"\item $8$" "\n", "")),
    ]
    report = check_split_output_fidelity(questions, source)

    assert report["checked"] == 2, report
    reasons = questions[1]["_fidelity_review"]
    assert any("选项变少" in r for r in reasons), reasons


def test_unlocatable_question_is_skipped_not_guessed():
    """定位不到源文切片时只能跳过该题。

    拿整卷数字冒充单题会造成大面积假告警。
    """

    source = "第一题题干内容在此\n---\n第二题题干内容在此"
    questions = [_q("一道源文里根本找不到的改写题干内容")]
    report = check_split_output_fidelity(questions, source)

    assert report["checked"] == 0
    assert report["unmatched"] == 1
    assert "_fidelity_review" not in questions[0]
    assert "定位失败" in format_fidelity_log(report)


def test_without_source_text_nothing_is_claimed_checked():
    """没有源文时必须明说跳过了，绝不谎称已核。"""

    questions = [_q(CHOICE_STEM), _q(FILLIN_STEM)]
    report = check_split_output_fidelity(questions, "")
    assert report["mode"] == "skipped"
    assert report["checked"] == 0
    assert "跳过" in format_fidelity_log(report)


def test_empty_and_malformed_inputs_never_raise():
    assert check_split_output_fidelity([], "")["review_count"] == 0
    assert check_split_output_fidelity(None, "x")["review_count"] == 0
    # 脏数据（非 dict 元素 / 字段不是字符串）不得炸掉整条导入链
    report = check_split_output_fidelity([None, _q(None, 5)], "随便一点源文内容")
    assert report["review_count"] == 0


def test_existing_image_review_flags_survive():
    """新标记不得覆盖既有的 `_image_review`（两种核对理由要能同时呈现）。"""

    trimmed = CHOICE_STEM.replace(r"\item $4$" "\n", "")
    questions = [_q(trimmed)]
    questions[0]["_image_review"] = ["failsafe"]
    check_split_output_fidelity(questions, CHOICE_STEM)

    assert questions[0]["_image_review"] == ["failsafe"]
    assert questions[0]["_fidelity_review"]


def test_log_lists_question_numbers_and_reasons():
    source = CHOICE_STEM + SEPARATOR + FILLIN_STEM
    questions = [
        _q(CHOICE_STEM.replace(r"\item $2$" "\n", "")),
        _q(FILLIN_STEM.replace(r"$\fillin$", "")),
    ]
    report = check_split_output_fidelity(questions, source)
    line = format_fidelity_log(report)

    assert "第 1 题" in line and "第 2 题" in line, line
    assert line.startswith("[内容自检]")


def test_feature_counters_are_not_silently_zero():
    """守住「计数器恒为 0」这一整类静默失效。

    实现过程中真踩过两次，都不报错、都表现为「永远没有异常」：
      * 行首锚点配 MULTILINE —— 切片与题面去过空白后没有行首，items 恒 0；
      * 用剥掉反斜杠的文本做切片 —— begin{choices} 变成纯文字，全部恒 0。
    所以这里直接对每个特征断言非零，而不是只测端到端有没有告警。
    """

    from mathbank.output_fidelity import _count_features

    sample = (
        CHOICE_STEM
        + "\n"
        + FILLIN_STEM
        + "\n"
        + TABLE_BODY
        + "\n"
        + r"[插图待补: 图3]"
    )
    features = _count_features(sample)
    assert features["choices"] == 1, features
    assert features["items"] == 4, features
    assert features["fillins"] == 2, features
    assert features["tables"] == 1, features
    assert features["figures"] == {"3"}, features

    # 去空白后仍要数得出来——切片的真实形态就是去空白的
    stripped = "".join(sample.split())
    assert _count_features(stripped) == features, _count_features(stripped)
