import pytest

from mathbank.question_types import (
    answer_choice_letters,
    detect_structured_question_form,
    enforce_multi_choice_from_answer,
    normalize_ai_question_form,
)


@pytest.mark.parametrize(
    ("content", "expected"),
    (
        (r"答案为\fillin", "fill_in_blank"),
        (r"答案为\FILLIN", "fill_in_blank"),
        (r"\begin{choices}\item A\end{choices}", "choice"),
        (r"\begin { choices }\item A\end{choices}", "choice"),
        ("请证明该结论", None),
    ),
)
def test_structured_question_form_detection(content, expected):
    assert detect_structured_question_form(content) == expected


def test_choices_environment_takes_precedence_over_blank_inside_an_option():
    content = r"\begin{choices}\item \fillin\item 2\end{choices}"

    assert detect_structured_question_form(content) == "choice"


@pytest.mark.parametrize(
    ("model_value", "expected"),
    (
        ("single_choice", "choice"),
        ("multi_choice", "choice"),
        ("单选题", "choice"),
        ("多选题", "choice"),
        ("fill_in_blank", "fill_in_blank"),
        ("detailed_answer", "detailed_answer"),
        ("unexpected", "unknown"),
        (None, "unknown"),
    ),
)
def test_ai_question_form_never_preserves_single_or_multi_choice(model_value, expected):
    assert normalize_ai_question_form(model_value) == expected


def test_choice_mark_letters_rescue_a_fill_in_blank_misclassification():
    r"""真实拆卷形态：选项横排、答案只有【详解】+``故选：BC``，题型却落成填空题。"""
    content = (
        "已知函数 $f(x) = \\begin{cases} |\\log_2(x - 1)|, & 1 < x \\leq 3 \\\\ "
        "x^2 - 8x + 16, & x > 3 \\end{cases}$ ，若方程 $y = f(x) - m$ 有 4 个不同的零点，则（）\n"
        "A. $0 < m < 2$ B. $x_1 + x_2 = x_1x_2$ C. $x_3 + x_4 = 8$ D. 最小值为 32"
    )
    answer = "【详解】由图知 $0<m<1$，A错；B、C对；D错.\n\n故选：BC"

    assert answer_choice_letters(answer) == "BC"
    assert enforce_multi_choice_from_answer("fill_in_blank", content, answer) == "multi_choice"


def test_choice_mark_never_upgrades_text_answers_without_a_choice_marker():
    answer = "【答案】A、B 两点之间的距离为 2."
    assert enforce_multi_choice_from_answer("detailed_answer", "求 $A$、$B$ 两点距离。", answer) == "detailed_answer"
