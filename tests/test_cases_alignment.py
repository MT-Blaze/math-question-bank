"""cases 对齐符清洗的回归测试：分段函数补 ``&``、方程组不动、aligned 遗留 ``&`` 清掉。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mathbank.latex_normalize import (
    align_cases_conditions,
    classify_cases_body,
    normalize_for_storage,
)


def _cases(body: str) -> str:
    return "$f(x)=\\begin{cases} %s \\end{cases}$" % body


@pytest.mark.parametrize(
    "body, expected",
    [
        ("1, x > 0 \\\\ -1, x < 0", "1, & x > 0 \\\\ -1, & x < 0"),
        ("x+1, x \\ge -1 \\\\ -1-x, x < -1", "x+1, & x \\ge -1 \\\\ -1-x, & x < -1"),
        ("2x-1，x>0 \\\\ x^{2}+1，x<0", "2x-1， & x>0 \\\\ x^{2}+1， & x<0"),
        ("b,a\\geq b\\\\ a,a<b", "b, & a\\geq b\\\\ a, & a<b"),
        ("1, x \\geq 0, \\\\ -1, x < 0", "1, & x \\geq 0, \\\\ -1, & x < 0"),
        ("x, x\\in A \\\\ 0, 其他", "x, & x\\in A \\\\ 0, & 其他"),
    ],
)
def test_piecewise_rows_get_alignment_amp(body, expected):
    status, new_body, _ = classify_cases_body(body)
    assert status == "aligned"
    assert new_body == expected


@pytest.mark.parametrize(
    "body",
    [
        "a > 0 \\\\ \\Delta = 4 - 4a \\leq 0",
        "x+y=3\\\\ x-y=2",
        "m - 1 < 3m + 3, \\\\ 3m + 3 \\leq -3",
        "a - 2 < 3a \\\\ a - 2 \\geqslant -2, \\\\ 3a \\leqslant 3",
        "g(a) = ka, \\\\ g(b) = kb",
    ],
)
def test_equation_and_inequality_systems_untouched(body):
    status, new_body, _ = classify_cases_body(body)
    assert status == "system"
    assert new_body is None
    assert align_cases_conditions(_cases(body)) == _cases(body)


@pytest.mark.parametrize(
    "body",
    ["1 & (x \\geq 0) \\\\ 0 & (x < 0)", "x+1, & x>0 \\\\ 0, & x\\le 0"],
)
def test_existing_proper_amp_untouched(body):
    status, _, _ = classify_cases_body(body)
    assert status == "has_amp"
    assert align_cases_conditions(_cases(body)) == _cases(body)


@pytest.mark.parametrize(
    "body, expected",
    [
        ("a&=0\\\\ \\Delta=a^{2}-4<0", "a=0\\\\ \\Delta=a^{2}-4<0"),
        ("2-a&>1\\\\ 2+a&<4,\\\\ a>0", "2-a>1\\\\ 2+a<4,\\\\ a>0"),
        ("f(-1)=f(2)=1\\\\ f(\\dfrac{1}{2})&\\geq0", "f(-1)=f(2)=1\\\\ f(\\dfrac{1}{2})\\geq0"),
        ("&m > 0 \\\\ &m^2 - 4m < 0", "m > 0 \\\\ m^2 - 4m < 0"),
        ("x+1,x&\\in[0,1) \\\\ 0,x&\\notin[0,1)", "x+1, & x\\in[0,1) \\\\ 0, & x\\notin[0,1)"),
    ],
)
def test_stale_aligned_amps_are_repaired(body, expected):
    status, new_body, _ = classify_cases_body(body)
    assert status == "stale_amp"
    assert new_body == expected


def test_leading_amp_after_row_break_never_glues_a_control_word():
    out = align_cases_conditions(_cases("&a>0\\\\&\\dfrac{1}{a}<1"))
    assert "\\\\\\dfrac" not in out
    assert "\\\\ \\dfrac" in out


@pytest.mark.parametrize(
    "body",
    [
        "t&\\leqslant-1\\\\ t&+2=1",
        "1, x>0 \\\\ x+y=2",
        "\\begin{array}{l} 1, x>0 \\end{array} \\\\ 0, x<0",
        "1, x>0, y>0 \\\\ 0, 其他",
    ],
)
def test_ambiguous_blocks_are_left_alone(body):
    status, new_body, reason = classify_cases_body(body)
    assert status == "ambiguous"
    assert new_body is None and reason
    assert align_cases_conditions(_cases(body)) == _cases(body)


@pytest.mark.parametrize(
    "body",
    [
        "f(x,y), x>0 \\\\ 0, x\\le0",
        "\\{1,2\\}, x\\in A \\\\ \\varnothing, x\\notin A",
        "x, x\\in[0,1) \\\\ 1, x\\in[1,2]",
        "a\\,b, x>0 \\\\ 0, x\\le0",
    ],
)
def test_commas_inside_brackets_and_thin_space_are_not_separators(body):
    status, new_body, _ = classify_cases_body(body)
    assert status == "aligned"
    assert new_body.count("&") == 2
    assert "f(x,y)" in new_body or "\\{1,2\\}" in new_body or "[0,1)" in new_body or "\\," in new_body


def test_storage_chain_aligns_and_is_idempotent():
    raw = "已知 $f(x)=\\left\\{\\begin{aligned} x+1, x>0 \\\\ a&=0 \\end{aligned}\\right.$"
    once = normalize_for_storage(raw, "detailed_answer", is_answer=True)
    assert "\\begin{cases}" in once and "\\left\\{" not in once
    assert normalize_for_storage(once, "detailed_answer", is_answer=True) == once

    piecewise = "设 $f(x)=\\begin{cases} 2x-1, x > 0 \\\\ x^{2}+1, x < 0 \\end{cases}$，求值。"
    stored = normalize_for_storage(piecewise, "fill_in_blank")
    assert "2x-1, & x > 0" in stored and "x^{2}+1, & x < 0" in stored
    assert normalize_for_storage(stored, "fill_in_blank") == stored


def test_storage_chain_leaves_systems_byte_identical():
    text = "由 $\\begin{cases} x+y=3\\\\ x-y=2 \\end{cases}$ 解得"
    assert "\\begin{cases} x+y=3\\\\ x-y=2 \\end{cases}" in normalize_for_storage(text, "detailed_answer")
